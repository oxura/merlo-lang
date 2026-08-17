"""Immutable canonical contracts for host intrinsics.

Every frontend and runtime adapter uses this table for intrinsic identity,
argument validation, result adaptation, effects, and ownership metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from merlo.type_parser import generic_parts


@dataclass(frozen=True)
class IntrinsicSignature:
    name: str
    parameters: tuple[str, ...]
    result_type: str
    effect: str
    capability: str
    parameter_ownership: tuple[str, ...] = ()
    result_ownership: str = "value"

    def __post_init__(self) -> None:
        if not self.parameter_ownership:
            object.__setattr__(self, "parameter_ownership", tuple("value" for _ in self.parameters))
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(f"ownership arity mismatch for {self.name}")
        if self.capability != self.effect:
            raise ValueError(f"capability/effect mismatch for {self.name}")

    @property
    def arity(self) -> int:
        return len(self.parameters)

    @property
    def ownership(self) -> tuple[str, ...]:
        """Compatibility spelling for consumers inspecting parameter ownership."""
        return self.parameter_ownership


@dataclass(frozen=True)
class InstanceMethodSignature:
    """A monomorphic method contract owned by the language, not the backend."""

    receiver_type: str
    name: str
    parameters: tuple[str, ...]
    result_type: str
    parameter_ownership: tuple[str, ...] = ()
    result_ownership: str = "value"
    receiver_ownership: str = "borrow"
    effects: tuple[str, ...] = ()
    static: bool = False
    abi_lowering: str | None = None
    representation_lowering: str | None = None
    operation_family: str | None = None
    contextual_numeric_result: bool = False

    def __post_init__(self) -> None:
        if not self.parameter_ownership:
            object.__setattr__(
                self,
                "parameter_ownership",
                tuple("value" for _ in self.parameters),
            )
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(
                f"ownership arity mismatch for {self.receiver_type}.{self.name}"
            )
        if self.receiver_ownership not in {"borrow", "borrow_mut", "consuming"}:
            raise ValueError(
                f"invalid receiver ownership for {self.receiver_type}.{self.name}"
            )
        if tuple(sorted(set(self.effects))) != self.effects:
            raise ValueError(
                f"effects must be canonical for {self.receiver_type}.{self.name}"
            )
        if self.static and self.receiver_ownership != "borrow":
            raise ValueError(
                f"static method cannot own a receiver: {self.receiver_type}.{self.name}"
            )
        if self.operation_family not in {
            None,
            "vec",
            "map",
            "box",
            "bytes_text",
        }:
            raise ValueError(
                f"invalid operation family for {self.receiver_type}.{self.name}"
            )
        if self.contextual_numeric_result and self.result_type != "UInt64":
            raise ValueError(
                "contextual numeric result requires canonical UInt64 for "
                f"{self.receiver_type}.{self.name}"
            )

    @property
    def arity(self) -> int:
        return len(self.parameters)

    def result_for(self, expected: str | None) -> str:
        if self.contextual_numeric_result and expected in {
            "Byte",
            "UInt64",
            "Int64",
            "Float32",
            "Float64",
        }:
            return expected
        return self.result_type


@dataclass(frozen=True)
class BuiltinFunctionSignature:
    """A symbolic typed contract for constructors and language-level calls."""

    name: str
    parameters: tuple[str, ...]
    result_type: str
    parameter_ownership: tuple[str, ...] = ()
    variadic: bool = False

    def __post_init__(self) -> None:
        if not self.parameter_ownership:
            object.__setattr__(
                self,
                "parameter_ownership",
                tuple("value" for _ in self.parameters),
            )
        if len(self.parameter_ownership) != len(self.parameters):
            raise ValueError(f"ownership arity mismatch for {self.name}")


def _signature(
    name: str,
    parameters: tuple[str, ...],
    result_type: str,
    effect: str,
    *,
    parameter_ownership: tuple[str, ...] | None = None,
    result_ownership: str = "value",
) -> IntrinsicSignature:
    return IntrinsicSignature(
        name,
        parameters,
        result_type,
        effect,
        effect,
        tuple("value" for _ in parameters) if parameter_ownership is None else parameter_ownership,
        result_ownership,
    )


# The mapping and all entries are immutable after module initialization.
_INTRINSIC_ROWS = (
    _signature("console.read", (), "Bytes", "console.read", result_ownership="owned"),
    _signature("console.read_line", (), "Text", "console.read", result_ownership="owned"),
    _signature("console.read_all", (), "Text", "console.read", result_ownership="owned"),
    _signature("console.write", ("TextView",), "Unit", "console.write", parameter_ownership=("borrow",)),
    _signature("fs.open_read", ("Path",), "Result[FileReader,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read", ("Path",), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_text", ("Path",), "Result[Text,FileError]", "fs.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.read_chunk", ("FileReader", "UInt64"), "Result[Bytes,FileError]", "fs.read", parameter_ownership=("borrow_mut", "value"), result_ownership="owned"),
    _signature("fs.open_write", ("Path",), "Result[FileWriter,FileError]", "fs.write", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("fs.write", ("Path", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_text", ("Path", "TextView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow", "borrow")),
    _signature("fs.write_chunk", ("FileWriter", "BytesView"), "Result[Unit,FileError]", "fs.write", parameter_ownership=("borrow_mut", "borrow")),
    _signature("fs.close_read", ("FileReader",), "Result[Unit,FileError]", "fs.read", parameter_ownership=("consuming",)),
    _signature("fs.close_write", ("FileWriter",), "Result[Unit,FileError]", "fs.write", parameter_ownership=("consuming",)),
    _signature("env.read", ("Text",), "Text", "env.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("env.get", ("Text",), "Text", "env.read", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("clock.now", (), "UInt64", "clock.now"),
    _signature("random.read", ("UInt64",), "Bytes", "random.read", result_ownership="owned"),
    _signature("network.tcp_connect", ("Text", "UInt64"), "Result[UInt64,AppError]", "network.tcp", parameter_ownership=("borrow", "value")),
    _signature("network.tcp_send", ("UInt64", "BytesView"), "Result[UInt64,AppError]", "network.tcp", parameter_ownership=("value", "borrow")),
    _signature("network.tcp_receive", ("UInt64", "UInt64"), "Result[Bytes,AppError]", "network.tcp", result_ownership="owned"),
    _signature("network.tcp_close", ("UInt64",), "Result[Unit,AppError]", "network.tcp"),
    _signature("network.http_request", ("Text",), "Result[Bytes,AppError]", "network.http", parameter_ownership=("borrow",), result_ownership="owned"),
    _signature("process.args", (), "UInt64", "process.args"),
    _signature("process.arg", ("UInt64",), "Text", "process.args", result_ownership="owned"),
)
INTRINSIC_SIGNATURES: Mapping[str, IntrinsicSignature] = MappingProxyType(
    {row.name: row for row in _INTRINSIC_ROWS}
)


_INSTANCE_METHOD_ROWS = (
    InstanceMethodSignature(
        "Text",
        "from_bytes",
        ("BytesView", "UInt64", "UInt64"),
        "Text",
        ("borrow", "value", "value"),
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        static=True,
        abi_lowering="merlo_text_from_bytes",
    ),
    InstanceMethodSignature(
        "TextBuilder",
        "new",
        (),
        "TextBuilder",
        result_ownership="owned",
        effects=("allocate", "may_fail"),
        static=True,
        abi_lowering="merlo_text_builder_new",
    ),
    InstanceMethodSignature(
        "Path",
        "to_text",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Bytes",
        "to_text",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Bytes",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Bytes",
        "view",
        (),
        "BytesView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "BytesView",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "BytesView",
        "byte",
        ("UInt64",),
        "UInt64",
        effects=("bounds_check",),
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "BytesView",
        "slice",
        ("UInt64", "UInt64"),
        "BytesView",
        result_ownership="borrow",
        effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "clone",
        (),
        "Text",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "len",
        (),
        "UInt64",
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Text",
        "byte",
        ("UInt64",),
        "UInt64",
        effects=("bounds_check",),
        operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Text",
        "as_view",
        (),
        "TextView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "view",
        (),
        "TextView",
        result_ownership="borrow",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "contains",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text",
        "contains_ascii_case_insensitive",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "starts_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "ends_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "Text", "slice_bytes", ("UInt64", "UInt64"), "TextView",
        result_ownership="borrow", effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "parse_uint64", (), "Result[UInt64,UInt64]",
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "is_ascii", (), "Bool", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "is_digits", (), "Bool", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "len", (), "UInt64", operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "TextView", "byte", ("UInt64",), "UInt64",
        effects=("bounds_check",), operation_family="bytes_text",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "TextView", "contains", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView",
        "contains_ascii_case_insensitive",
        ("Text",),
        "Bool",
        parameter_ownership=("borrow",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "slice_bytes", ("UInt64", "UInt64"), "TextView",
        result_ownership="borrow", effects=("bounds_check",),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "starts_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "ends_with", ("Text",), "Bool",
        parameter_ownership=("borrow",), operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextView", "to_text", (), "Text", result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_text", ("Text",), "Unit",
        parameter_ownership=("borrow",), receiver_ownership="borrow_mut",
        effects=("allocate", "copy", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_byte", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_scalar", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "finish", (), "Text", result_ownership="owned",
        receiver_ownership="consuming", operation_family="bytes_text",
    ),
    InstanceMethodSignature(
        "TextBuilder", "append_uint64", ("UInt64",), "Unit",
        receiver_ownership="borrow_mut", effects=("allocate", "may_fail"),
        operation_family="bytes_text",
    ),
    InstanceMethodSignature("FileReader", "lines", (), "FileLines", receiver_ownership="borrow_mut"),
    InstanceMethodSignature("FileLines", "count_text", (), "Text", result_ownership="owned", receiver_ownership="borrow_mut"),
    InstanceMethodSignature(
        "Option[T]",
        "is_none",
        (),
        "Bool",
        representation_lowering="option_is_none",
    ),
    InstanceMethodSignature(
        "Option[T]",
        "is_some",
        (),
        "Bool",
        representation_lowering="option_is_some",
    ),
    InstanceMethodSignature(
        "Option[T]",
        "unwrap",
        (),
        "T",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="option_unwrap_clone",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "is_ok",
        (),
        "Bool",
        representation_lowering="result_is_ok",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "is_err",
        (),
        "Bool",
        representation_lowering="result_is_err",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "unwrap",
        (),
        "T",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="result_unwrap_clone",
    ),
    InstanceMethodSignature(
        "Result[T,E]",
        "unwrap_err",
        (),
        "E",
        result_ownership="payload_clone",
        effects=("allocate", "copy", "may_fail"),
        representation_lowering="result_unwrap_err_clone",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "clone",
        (),
        "Vec[T]",
        result_ownership="owned",
        effects=("allocate", "copy", "may_fail"),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "push",
        ("T",),
        "Unit",
        parameter_ownership=("consuming",),
        receiver_ownership="borrow_mut",
        effects=("allocate", "may_fail"),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "len",
        (),
        "UInt64",
        operation_family="vec",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "capacity",
        (),
        "UInt64",
        operation_family="vec",
        contextual_numeric_result=True,
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "get",
        ("UInt64",),
        "T",
        result_ownership="borrow",
        effects=("bounds_check",),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "get_mut",
        ("UInt64",),
        "T",
        result_ownership="borrow_mut",
        receiver_ownership="borrow_mut",
        effects=("bounds_check",),
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Vec[T]",
        "view",
        (),
        "Borrow[Vec[T]]",
        result_ownership="borrow",
        operation_family="vec",
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "insert",
        ("K", "V"),
        "Unit",
        parameter_ownership=("borrow", "value"),
        receiver_ownership="borrow_mut",
        effects=("allocate", "copy", "may_fail"),
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "get",
        ("K",),
        "V",
        parameter_ownership=("borrow",),
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Map[K,V]",
        "entries",
        (),
        "Borrow[Map[K,V]]",
        result_ownership="borrow",
        operation_family="map",
    ),
    InstanceMethodSignature(
        "Box[T]",
        "get",
        (),
        "T",
        result_ownership="borrow",
        operation_family="box",
    ),
    InstanceMethodSignature(
        "Box[T]",
        "get_mut",
        (),
        "T",
        result_ownership="borrow_mut",
        receiver_ownership="borrow_mut",
        operation_family="box",
    ),
)
INSTANCE_METHOD_SIGNATURES: Mapping[
    tuple[str, str], InstanceMethodSignature
] = MappingProxyType(
    {(row.receiver_type, row.name): row for row in _INSTANCE_METHOD_ROWS}
)
INSTANCE_METHOD_NAMES = frozenset(row.name for row in _INSTANCE_METHOD_ROWS)

# These symbolic variables are resolved by type-directed elaboration. Keeping
# constructors and language calls beside host/method contracts prevents the
# binder from growing a second manually synchronized builtin API table.
_BUILTIN_FUNCTION_ROWS = (
    BuiltinFunctionSignature("Path", ("Text",), "Path", ("borrow",)),
    BuiltinFunctionSignature("Unit", (), "Unit"),
    BuiltinFunctionSignature("Ok", ("T",), "Result[T,E]"),
    BuiltinFunctionSignature("Err", ("E",), "Result[T,E]"),
    BuiltinFunctionSignature("Some", ("T",), "Option[T]"),
    BuiltinFunctionSignature("None", (), "Option[T]"),
    BuiltinFunctionSignature("not", ("Bool",), "Bool"),
    BuiltinFunctionSignature("and", ("Bool", "Bool"), "Bool"),
    BuiltinFunctionSignature("or", ("Bool", "Bool"), "Bool"),
    BuiltinFunctionSignature("drop", ("T",), "Unit", ("consuming",)),
    BuiltinFunctionSignature("move", ("T",), "T", ("consuming",)),
    BuiltinFunctionSignature("map", ("Collection[T]", "Fn[T,U]"), "Collection[U]"),
    BuiltinFunctionSignature("filter", ("Collection[T]", "Fn[T,Bool]"), "Collection[T]"),
    BuiltinFunctionSignature("fold", ("Collection[T]", "U", "Fn[U,T,U]"), "U"),
    BuiltinFunctionSignature("len", ("Collection[T]",), "UInt64", ("borrow",)),
    BuiltinFunctionSignature("release", ("Borrow[T]",), "Unit"),
    BuiltinFunctionSignature("wrapping_add", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("wrapping_sub", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("wrapping_mul", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_add", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_sub", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("checked_mul", ("Integer", "Integer"), "Integer"),
    BuiltinFunctionSignature("__merlo_try__", ("Result[T,E]",), "T"),
    BuiltinFunctionSignature("Byte", ("Scalar",), "Byte"),
    BuiltinFunctionSignature("UInt64", ("Scalar",), "UInt64"),
    BuiltinFunctionSignature("Int64", ("Scalar",), "Int64"),
    BuiltinFunctionSignature("Float32", ("Scalar",), "Float32"),
    BuiltinFunctionSignature("Float64", ("Scalar",), "Float64"),
)
BUILTIN_FUNCTION_SIGNATURES: Mapping[str, BuiltinFunctionSignature] = MappingProxyType(
    {row.name: row for row in _BUILTIN_FUNCTION_ROWS}
)
BUILTIN_FUNCTIONS = frozenset(BUILTIN_FUNCTION_SIGNATURES)
BUILTIN_RECEIVERS = frozenset(
    {name.partition(".")[0] for name in INTRINSIC_SIGNATURES}
    | {
        "Text", "Bytes", "TextBuilder", "Vec", "Map", "Box", "Option",
        "Result",
    }
)


_ABI_LOWERINGS: Mapping[str, str] = MappingProxyType(
    {
        "console.read": "merlo_console_read",
        "console.read_line": "merlo_console_read_line",
        "console.read_all": "merlo_console_read_all",
        "console.write": "merlo_console_write_view",
        "fs.open_read": "merlo_file_open_read",
        "fs.read": "merlo_file_read_all",
        "fs.read_text": "merlo_file_read_text",
        "fs.read_chunk": "merlo_file_read_chunk",
        "fs.open_write": "merlo_file_open_write",
        "fs.write": "merlo_file_write_all",
        "fs.write_text": "merlo_file_write_text",
        "fs.write_chunk": "merlo_file_write_chunk",
        "fs.close_read": "merlo_file_close",
        "fs.close_write": "merlo_file_close_writer",
        "env.read": "merlo_env_read",
        "env.get": "merlo_env_read",
        "clock.now": "merlo_clock_now",
        "random.read": "merlo_random_read",
        "network.tcp_connect": "merlo_network_tcp_connect",
        "network.tcp_send": "merlo_network_tcp_send",
        "network.tcp_receive": "merlo_network_tcp_receive",
        "network.tcp_close": "merlo_network_tcp_close",
        "network.http_request": "merlo_network_http_request",
        "process.args": "merlo_process_args_count",
        "process.arg": "merlo_process_arg",
    }
)


@dataclass(frozen=True)
class BuiltinContractGraph:
    """Single immutable view consumed by binder, elaborator, and backend."""

    intrinsics: Mapping[str, IntrinsicSignature]
    methods: Mapping[tuple[str, str], InstanceMethodSignature]
    functions: Mapping[str, BuiltinFunctionSignature]
    abi_lowerings: Mapping[str, str]

    def __post_init__(self) -> None:
        missing = set(self.intrinsics) - set(self.abi_lowerings)
        if missing:
            raise ValueError(f"intrinsics without ABI lowering: {sorted(missing)}")

    def intrinsic(self, symbol: str) -> IntrinsicSignature | None:
        return self.intrinsics.get(symbol)

    def method(
        self,
        receiver_type: str,
        name: str,
    ) -> InstanceMethodSignature | None:
        exact = self.methods.get((receiver_type, name))
        if exact is not None:
            return exact
        for (pattern, method_name), signature in self.methods.items():
            if method_name != name or "[" not in pattern:
                continue
            constructor = pattern.partition("[")[0]
            pattern_parts = generic_parts(pattern, constructor)
            actual_parts = generic_parts(
                receiver_type,
                constructor,
                arity=len(pattern_parts) if pattern_parts is not None else None,
            )
            if pattern_parts is None or actual_parts is None:
                continue
            substitutions = dict(zip(pattern_parts, actual_parts, strict=True))

            def instantiate(type_name: str) -> str:
                result = type_name
                for variable, concrete in substitutions.items():
                    result = re.sub(
                        rf"\b{re.escape(variable)}\b",
                        concrete,
                        result,
                    )
                return result

            return replace(
                signature,
                receiver_type=receiver_type,
                parameters=tuple(
                    instantiate(parameter)
                    for parameter in signature.parameters
                ),
                result_type=instantiate(signature.result_type),
            )
        return None

    def static_method(
        self,
        receiver_type: str,
        name: str,
    ) -> InstanceMethodSignature | None:
        signature = self.method(receiver_type, name)
        return signature if signature is not None and signature.static else None

    def has_representation_method(self, name: str) -> bool:
        return any(
            method_name == name
            and signature.representation_lowering is not None
            for (_receiver, method_name), signature in self.methods.items()
        )

    def abi_lowering(self, symbol: str) -> str | None:
        lowering = self.abi_lowerings.get(symbol)
        if lowering is not None:
            return lowering
        receiver, separator, method = symbol.partition(".")
        if not separator:
            return None
        signature = self.methods.get((receiver, method))
        return signature.abi_lowering if signature is not None else None


CONTRACT_GRAPH = BuiltinContractGraph(
    INTRINSIC_SIGNATURES,
    INSTANCE_METHOD_SIGNATURES,
    BUILTIN_FUNCTION_SIGNATURES,
    _ABI_LOWERINGS,
)


def intrinsic_signature(name: str) -> IntrinsicSignature | None:
    return CONTRACT_GRAPH.intrinsic(name)


def contextual_result_type(result_type: str, expected: str | None) -> str:
    """Preserve a caller's Result error row while using canonical Ok type."""
    if not result_type.startswith("Result[") or not expected or not expected.startswith("Result["):
        return result_type
    canonical = result_type[7:-1].split(",", 1)
    contextual = expected[7:-1].split(",", 1)
    if len(canonical) == 2 and len(contextual) == 2 and canonical[0] == contextual[0]:
        return f"Result[{canonical[0]},{contextual[1]}]"
    return result_type


def format_intrinsic_arity(signature: IntrinsicSignature, actual: int) -> str:
    return (
        f"IntrinsicArityMismatch: {signature.name} expects {signature.arity} "
        f"argument(s), got {actual}"
    )


INTRINSIC_EFFECTS = frozenset(row.effect for row in _INTRINSIC_ROWS)

__all__ = [
    "BUILTIN_FUNCTIONS",
    "BUILTIN_FUNCTION_SIGNATURES",
    "BUILTIN_RECEIVERS",
    "BuiltinFunctionSignature",
    "BuiltinContractGraph",
    "CONTRACT_GRAPH",
    "INTRINSIC_EFFECTS",
    "INTRINSIC_SIGNATURES",
    "INSTANCE_METHOD_NAMES",
    "INSTANCE_METHOD_SIGNATURES",
    "InstanceMethodSignature",
    "IntrinsicSignature",
    "contextual_result_type",
    "format_intrinsic_arity",
    "intrinsic_signature",
]
